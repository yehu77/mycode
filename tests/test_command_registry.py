from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.commands import CommandExecution, build_default_command_registry
from claudecode_py.config import SessionConfig
from claudecode_py.session import Session
from claudecode_py.state import PlanningArtifact, SessionState
from claudecode_py.storage.transcript import save_transcript


class CommandRegistryTests(unittest.TestCase):
    def test_registry_renders_help_and_handles_builtin_command(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        help_text = registry.render_help()
        handled, output = registry.handle(session, "/tools")

        self.assertIn("/tools", help_text)
        self.assertTrue(handled)
        assert output is not None
        self.assertIn("list_dir:", output)
        self.assertIn("/review", help_text)
        self.assertIn("/commit", help_text)
        self.assertIn("/init", help_text)
        self.assertIn("/install", help_text)
        self.assertIn("/insights", help_text)
        self.assertIn("/advisor", help_text)
        self.assertIn("/plan", help_text)
        self.assertIn("/security-review", help_text)
        self.assertIn("/ultraplan", help_text)

    def test_registry_handles_commands_with_arguments(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_command_registry"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".pyclaude" / "skills").mkdir(parents=True)
        (cwd / ".pyclaude" / "skills" / "review.md").write_text(
            "Review carefully.",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            registry = build_default_command_registry()
            handled, output = registry.handle(session, "/skills-enable review")

            self.assertTrue(handled)
            self.assertEqual(output, 'Enabled skill "review".')
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_registry_returns_prompt_execution_for_review(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            handled, output = registry.handle(session, "/review 123")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("PR number or selector: 123", output.prompt)
            self.assertIn("bash", output.allowed_tool_names)
            self.assertIn("gh pr diff", output.allowed_bash_command_prefixes)
        finally:
            session.close()

    def test_registry_returns_prompt_execution_for_commit(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            with patch(
                "claudecode_py.commands.prompt_commands._run_shell_capture",
                side_effect=["status", "diff", "branch", "log"],
            ):
                handled, output = registry.handle(session, "/commit")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("status", output.prompt)
            self.assertEqual(output.allowed_tool_names, ("bash",))
            self.assertEqual(
                output.allowed_bash_command_prefixes,
                ("git add", "git status", "git commit"),
            )
        finally:
            session.close()

    def test_registry_returns_prompt_execution_for_init(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            handled, output = registry.handle(session, "/init")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("CLAUDE.md", output.prompt)
            self.assertIn("write_file", output.allowed_tool_names)
            self.assertIn("apply_patch", output.allowed_tool_names)
        finally:
            session.close()

    def test_registry_returns_prompt_execution_for_security_review(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            with patch(
                "claudecode_py.commands.prompt_commands._run_shell_capture",
                side_effect=["status", "files", "commits", "diff"],
            ):
                handled, output = registry.handle(session, "/security-review")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("status", output.prompt)
            self.assertIn("diff", output.prompt)
            self.assertIn("read_file", output.allowed_tool_names)
            self.assertIn("git diff", output.allowed_bash_command_prefixes)
        finally:
            session.close()

    def test_registry_returns_prompt_execution_for_ultraplan(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            handled, output = registry.handle(session, "/ultraplan refactor the plugin loader")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("refactor the plugin loader", output.prompt)
            self.assertIn("read-only", output.prompt)
            self.assertIn("agent", output.allowed_tool_names)
            self.assertIn("find_symbol_graph", output.allowed_tool_names)
            self.assertIn("task_wait", output.allowed_tool_names)
            self.assertIn("git diff", output.allowed_bash_command_prefixes)
            self.assertTrue(output.require_read_only_subagents)
        finally:
            session.close()

    def test_registry_handles_install_and_advisor_commands(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        registry = build_default_command_registry()

        try:
            handled_install, output_install = registry.handle(session, "/install anthropic")
            handled_advisor_set, output_advisor_set = registry.handle(session, "/advisor opus")
            handled_advisor_show, output_advisor_show = registry.handle(session, "/advisor")
            handled_advisor_unset, output_advisor_unset = registry.handle(session, "/advisor off")

            self.assertTrue(handled_install)
            self.assertTrue(handled_advisor_set)
            self.assertTrue(handled_advisor_show)
            self.assertTrue(handled_advisor_unset)
            assert output_install is not None
            assert output_advisor_set is not None
            assert output_advisor_show is not None
            assert output_advisor_unset is not None
            self.assertIn("profile: anthropic", output_install)
            self.assertIn("pip install -e", output_install)
            self.assertIn("claude-3-opus-latest", output_advisor_set)
            self.assertIn("Advisor: claude-3-opus-latest", output_advisor_show)
            self.assertIn("Advisor disabled", output_advisor_unset)
        finally:
            session.close()

    def test_session_run_command_applies_tool_and_bash_restrictions(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Create a commit",
            allowed_tool_names=("bash",),
            allowed_bash_command_prefixes=("git status", "git commit"),
        )

        try:
            def fake_run_query_loop(active_session, prompt: str, sink=None):
                del sink
                self.assertEqual(prompt, "Create a commit")
                self.assertEqual([item["name"] for item in active_session.tool_specs()], ["bash"])
                self.assertTrue(active_session.is_bash_command_allowed("git status"))
                self.assertTrue(active_session.is_bash_command_allowed("git status && git commit -m test"))
                self.assertFalse(active_session.is_bash_command_allowed("git add ."))
                return "done"

            with patch("claudecode_py.session.run_query_loop", side_effect=fake_run_query_loop):
                result = session.run_command(execution)

            self.assertEqual(result, "done")
        finally:
            session.close()

    def test_session_run_command_can_force_read_only_subagents(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Plan the refactor",
            allowed_tool_names=("agent", "task_list"),
            require_read_only_subagents=True,
        )

        try:
            def fake_run_query_loop(active_session, prompt: str, sink=None):
                del sink
                self.assertEqual(prompt, "Plan the refactor")
                self.assertEqual(
                    [item["name"] for item in active_session.tool_specs()],
                    ["agent", "task_list"],
                )
                self.assertTrue(active_session.requires_read_only_subagents())
                return "planned"

            with patch("claudecode_py.session.run_query_loop", side_effect=fake_run_query_loop):
                result = session.run_command(execution)

            self.assertEqual(result, "planned")
            self.assertFalse(session.requires_read_only_subagents())
        finally:
            session.close()

    def test_session_run_command_routes_ultraplan_metadata(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="ignored by ultraplan",
            metadata={
                "command_kind": "ultraplan",
                "goal": "map the runtime",
                "scout_categories": ["architecture-boundaries", "risks-unknowns"],
            },
        )

        try:
            with patch.object(session, "run_ultraplan", return_value="plan") as run_ultraplan:
                result = session.run_command(execution)

            self.assertEqual(result, "plan")
            run_ultraplan.assert_called_once()
            self.assertEqual(run_ultraplan.call_args.kwargs["goal"], "map the runtime")
            self.assertEqual(
                run_ultraplan.call_args.kwargs["scout_categories"],
                ("architecture-boundaries", "risks-unknowns"),
            )
        finally:
            session.close()

    def test_plan_derive_returns_ultraplan_execution_with_parent_metadata(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        session.record_planning_artifact(
            PlanningArtifact(
                kind="ultraplan",
                goal="map runtime",
                summary="Focus on session.py.",
                used_read_only_subagents=True,
            )
        )
        session.state.last_plan_drift_status = "block"
        session.state.last_plan_drift_reason = "Narrow the implementation scope."
        session.record_plan_drift_context(
            "active_plan_goal: map runtime\n"
            "pending_tools: write_file\n"
            "active_plan_vs_candidate_diff:\n"
            "- cli.py drift"
        )
        registry = build_default_command_registry()

        try:
            handled, output = registry.handle(session, "/plan derive tighten runtime plan")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("tighten runtime plan", output.prompt)
            self.assertIn("revising an existing active plan", output.prompt.lower())
            self.assertEqual(
                output.metadata["supersede_artifact_id"],
                session.active_planning_artifact().artifact_id,  # type: ignore[union-attr]
            )
            self.assertTrue(output.metadata["derived_from_drift"])
            self.assertEqual(output.metadata["derivation_reason"], "Narrow the implementation scope.")
        finally:
            session.close()

    def test_registry_handles_insights_command(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_command_registry_insights"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)
        save_transcript(
            SessionConfig(cwd=cwd, interactive=False),
            SessionState(
                session_id="insight-a",
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "read_file", "input": {"path": "demo.py"}}],
                    },
                ],
            ),
        )
        session = Session(SessionConfig(cwd=cwd, interactive=False))
        registry = build_default_command_registry()

        try:
            handled, output = registry.handle(session, "/insights")

            self.assertTrue(handled)
            assert output is not None
            self.assertIn("workspace insights", output)
            self.assertIn("sessions: 1", output)
            self.assertIn("top_tools: read_file=1", output)
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)


if __name__ == "__main__":
    unittest.main()
