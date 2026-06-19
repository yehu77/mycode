from pathlib import Path
import json
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.agents import (
    AgentDefinition,
    AgentDefinitionRegistry,
    BUILTIN_EXPLORE_AGENT_NAME,
    BUILTIN_PLAN_AGENT_NAME,
    BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
    build_builtin_agent_registry,
    builtin_planning_agent_names,
    get_builtin_planning_agent_definitions,
    load_project_local_agent_registry,
    merge_agent_registries,
    validate_builtin_planning_agent_registry,
)
from claudecode_py.config import SessionConfig
from claudecode_py.runtime.plan_mode_v2 import build_plan_mode_full_attachment
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
            ["Explore", "Plan", "background", "default", "isolated_workspace", "read_only_planning"],
        )

    def test_builtin_planning_agent_identity_is_stable_and_registry_backed(self) -> None:
        registry = build_builtin_agent_registry()

        names = builtin_planning_agent_names()
        definitions = get_builtin_planning_agent_definitions(registry)

        self.assertEqual(names, (BUILTIN_EXPLORE_AGENT_NAME, BUILTIN_PLAN_AGENT_NAME))
        self.assertEqual(tuple(definition.name for definition in definitions), names)
        self.assertTrue(all(definition.source == "builtin" for definition in definitions))
        self.assertTrue(
            all(definition.based_on == BUILTIN_READ_ONLY_PLANNING_AGENT_NAME for definition in definitions)
        )
        self.assertNotIn("Verification", names)

    def test_builtin_planning_agent_lookup_uses_name_identity_not_registry_insertion_order(self) -> None:
        registry = AgentDefinitionRegistry()
        registry.add_definition(
            AgentDefinition(
                name=BUILTIN_PLAN_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        registry.add_definition(
            AgentDefinition(
                name=BUILTIN_EXPLORE_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        registry.add_definition(
            AgentDefinition(
                name=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
                source="builtin",
            )
        )

        definitions = get_builtin_planning_agent_definitions(registry)

        self.assertEqual(
            tuple(definition.name for definition in definitions),
            builtin_planning_agent_names(),
        )

    def test_validate_builtin_planning_agent_registry_rejects_runtime_policy_drift(self) -> None:
        background_registry = AgentDefinitionRegistry()
        background_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
                source="builtin",
                execution="background-session",
                tool_policy="read-only-subagent",
            )
        )
        background_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_EXPLORE_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        background_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_PLAN_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        with self.assertRaises(ValueError) as background_error:
            validate_builtin_planning_agent_registry(background_registry)
        self.assertIn('execution="child-session"', str(background_error.exception))

        writable_registry = AgentDefinitionRegistry()
        writable_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
                source="builtin",
                execution="child-session",
                tool_policy="write-enabled",
            )
        )
        writable_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_EXPLORE_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        writable_registry.add_definition(
            AgentDefinition(
                name=BUILTIN_PLAN_AGENT_NAME,
                source="builtin",
                based_on=BUILTIN_READ_ONLY_PLANNING_AGENT_NAME,
            )
        )
        with self.assertRaises(ValueError) as writable_error:
            validate_builtin_planning_agent_registry(writable_registry)
        self.assertIn('tool_policy="read-only-subagent"', str(writable_error.exception))

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

    def test_merge_agent_registries_project_local_cannot_shadow_builtin_planning_agents(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_agent_registry_planning_shadow"
        if cwd.exists():
            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        self._write_agent_definition(
            cwd,
            "explore.json",
            {
                "name": BUILTIN_EXPLORE_AGENT_NAME,
                "notes": "Project-local explore override.",
            },
        )

        try:
            registry = merge_agent_registries(
                build_builtin_agent_registry(),
                load_project_local_agent_registry(cwd),
            )

            effective = registry.get_definition(BUILTIN_EXPLORE_AGENT_NAME)
            diagnostics = registry.list_diagnostics()

            assert effective is not None
            self.assertEqual(effective.source, "builtin")
            self.assertTrue(
                any(
                    diagnostic.name == BUILTIN_EXPLORE_AGENT_NAME
                    and "cannot override builtin planning agents" in diagnostic.error
                    for diagnostic in diagnostics
                )
            )
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
            self.assertIn("- builtin: definitions=6 effective=5 shadowed=1", rendered)
            self.assertIn("- background: source=project-local effective=yes override_state=override", rendered)
            self.assertIn("- docs_helper: source=project-local effective=yes override_state=added", rendered)
            self.assertIn("- Explore: source=builtin effective=yes override_state=base based_on=read_only_planning", rendered)
            self.assertIn("- Plan: source=builtin effective=yes override_state=base based_on=read_only_planning", rendered)
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

    def test_plan_mode_workflow_attachment_uses_registry_backed_planning_agent_names(self) -> None:
        attachment = build_plan_mode_full_attachment(
            workflow_mode="five_phase",
            plan_file_path=Path("C:/tmp/current-plan.md"),
            plan_exists=False,
            config=SessionConfig(cwd=Path.cwd(), interactive=False),
        )

        self.assertIn(f"only use the {BUILTIN_EXPLORE_AGENT_NAME} agent type", attachment.text)
        self.assertIn(f"Launch {BUILTIN_PLAN_AGENT_NAME} agent(s)", attachment.text)
        self.assertIn("relevant files, existing implementations, reuse candidates, and code path traces", attachment.text)
        self.assertIn("reduce uncertainty for Phase 2, not replace the final implementation-design work", attachment.text)
        self.assertIn("ask for ordered implementation steps, critical files to change, reuse notes, verification ideas", attachment.text)
        self.assertIn("is the Phase 2 design role", attachment.text)
        self.assertIn("Keep final review, user clarification, and plan-file writing in the main thread", attachment.text)
        for name in builtin_planning_agent_names():
            self.assertIn(name, attachment.text)
