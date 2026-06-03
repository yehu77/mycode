from pathlib import Path
import json
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.agents import (
    build_builtin_agent_registry,
    load_project_local_agent_registry,
    merge_agent_registries,
)
from claudecode_py.config import SessionConfig
from claudecode_py.session import Session


class AgentRegistryTests(unittest.TestCase):
    def _write_agent_definition(self, cwd: Path, filename: str, payload: dict) -> Path:
        agents_dir = cwd / ".pyclaude" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        path = agents_dir / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_builtin_agent_registry_exposes_default_definitions(self) -> None:
        registry = build_builtin_agent_registry()

        names = [definition.name for definition in registry.list_effective_definitions()]

        self.assertEqual(
            names,
            ["background", "default", "isolated_workspace", "read_only_planning"],
        )

    def test_merge_agent_registries_project_local_same_name_shadows_builtin(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_agent_registry_shadow"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_agent_definition(
            cwd,
            "background.json",
            {
                "name": "background",
                "notes": "Custom background notes.",
            },
        )

        try:
            registry = merge_agent_registries(
                build_builtin_agent_registry(),
                load_project_local_agent_registry(cwd),
            )

            effective = registry.get_definition("background")
            shadowed = registry.list_shadowed_definitions()

            assert effective is not None
            self.assertEqual(effective.source, "project-local")
            self.assertEqual(effective.notes, "Custom background notes.")
            self.assertEqual(len(shadowed), 1)
            self.assertEqual(shadowed[0].name, "background")
            self.assertEqual(shadowed[0].source, "builtin")
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_invalid_project_local_agent_definition_surfaces_diagnostic(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_agent_registry_invalid"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_agent_definition(
            cwd,
            "broken.json",
            {
                "name": "broken",
                "execution": "unsupported-mode",
            },
        )

        try:
            registry = load_project_local_agent_registry(cwd)
            diagnostics = registry.list_diagnostics()

            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].name, "broken")
            self.assertIn("execution", diagnostics[0].error)
        finally:
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_session_agents_shows_project_local_added_override_and_diagnostic(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_session_agents_project_local"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_agent_definition(
            cwd,
            "background.json",
            {
                "name": "background",
                "notes": "Local background override.",
            },
        )
        self._write_agent_definition(
            cwd,
            "docs_helper.json",
            {
                "name": "docs_helper",
                "based_on": "default",
                "execution": "foreground",
                "project_memory": "disable",
                "skills_mode": "explicit",
                "enabled_skills": ["review"],
                "notes": "Project docs helper.",
            },
        )
        self._write_agent_definition(
            cwd,
            "broken.json",
            {
                "name": "broken",
                "based_on": "missing_builtin",
            },
        )

        session = Session(SessionConfig(cwd=cwd, interactive=False))
        try:
            rendered = session.describe_agents()

            self.assertIn("source summary:", rendered)
            self.assertIn("- project-local: definitions=2 effective=2 shadowed=0", rendered)
            self.assertIn("- builtin: definitions=4 effective=3 shadowed=1", rendered)
            self.assertIn("- background: source=project-local effective=yes override_state=override", rendered)
            self.assertIn("- docs_helper: source=project-local effective=yes override_state=added", rendered)
            self.assertIn("based_on=default", rendered)
            self.assertIn("memory=project_memory=disabled sharing=none", rendered)
            self.assertIn("skills=mode=explicit enabled=review", rendered)
            self.assertIn("diagnostics:", rendered)
            self.assertIn("- broken: source=project-local", rendered)
            self.assertIn("missing_builtin", rendered)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_reload_project_context_refreshes_project_local_agents(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_agent_registry_reload"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        session = Session(SessionConfig(cwd=cwd, interactive=False))

        try:
            self.assertNotIn("docs_helper", session.describe_agents())
            self._write_agent_definition(
                cwd,
                "docs_helper.json",
                {
                    "name": "docs_helper",
                    "based_on": "default",
                    "notes": "Reloaded docs helper.",
                },
            )

            message = session.reload_project_context()
            rendered = session.describe_agents()

            self.assertIn("Reloaded project context.", message)
            self.assertIn("agent_definition_changed=yes", message)
            self.assertIn("- docs_helper: source=project-local effective=yes override_state=added", rendered)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)
