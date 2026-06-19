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
        self.assertIn("/planning", help_text)
        self.assertIn("/security-review", help_text)
        self.assertIn("/ultraplan", help_text)

    def test_plan_command_enters_plan_mode_and_creates_plan_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plan_mode_command"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            handled, output = session.handle_repl_command("/plan")

            self.assertTrue(handled)
            self.assertEqual(session.state.session_runtime_mode, "plan")
            plan_path = session.get_plan_file_path()
            self.assertTrue(plan_path.exists())
            self.assertIn("Enabled plan mode.", str(output))
            self.assertIn(str(plan_path), str(output))
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_plan_command_with_request_returns_prompt_execution(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plan_mode_request"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            handled, output = session.handle_repl_command("/plan map runtime ownership")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertEqual(session.state.session_runtime_mode, "plan")
            self.assertEqual(output.prompt, "map runtime ownership")
            self.assertEqual(output.metadata["command_policy_source"], "repl:/plan")
            self.assertTrue(session.get_plan_file_path().exists())
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_plan_command_shows_current_plan_file_when_already_active(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plan_mode_show"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.enter_plan_mode()
            session.get_plan_file_path().write_text("# Current plan\n- inspect\n", encoding="utf-8")

            handled, output = session.handle_repl_command("/plan")

            self.assertTrue(handled)
            self.assertIn("plan_mode: active", str(output))
            self.assertIn("# Current plan", str(output))
            self.assertIn("- inspect", str(output))
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_planning_command_keeps_legacy_artifact_surface(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        try:
            session.record_planning_artifact(
                PlanningArtifact(
                    kind="ultraplan",
                    goal="map runtime",
                    summary="Current Architecture\n- runtime",
                    used_read_only_subagents=True,
                )
            )

            handled, output = session.handle_repl_command("/planning timeline")

            self.assertTrue(handled)
            self.assertIn("timeline_filter: all", str(output))
        finally:
            session.close()

    def test_clear_plan_command_clears_mode_and_plan_file(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_plan_mode_clear"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        cwd.mkdir(parents=True)

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            session.enter_plan_mode()
            plan_path = session.get_plan_file_path()
            plan_path.write_text("draft plan", encoding="utf-8")

            handled, output = session.handle_repl_command("/clear plan")

            self.assertTrue(handled)
            self.assertEqual(session.state.session_runtime_mode, "default")
            self.assertIsNone(session.state.plan_slug)
            self.assertFalse(plan_path.exists())
            self.assertIn("Cleared session plan file", str(output))
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

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
            self.assertEqual(output.metadata["command_policy_name"], "review")
        finally:
            session.close()

    def test_session_registry_registers_user_invocable_skill_commands(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_commands"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "arguments:\n"
            "  - version\n"
            "  - notes\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Bash(git status:*, git tag:*)\n"
            "model: claude-opus-4-6\n"
            "effort: high\n"
            "---\n\n"
            "Release version: $version\n"
            "Notes: ${notes}\n"
            "Dir: ${CLAUDE_SKILL_DIR}\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            handled, output = session.handle_repl_command('/ship "1.2.3" release-notes here')

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertIn("Release version: 1.2.3", output.prompt)
            self.assertIn("Notes: release-notes here", output.prompt)
            self.assertIn(str(cwd / ".claude" / "skills" / "ship"), output.prompt)
            self.assertEqual(output.allowed_tool_names, ("read_file", "bash"))
            self.assertEqual(output.allowed_bash_command_prefixes, ("git status", "git tag"))
            self.assertEqual(output.metadata["command_policy_name"], "skill:ship")
            self.assertEqual(output.metadata["skill_model_override"], "claude-opus-4-6")
            self.assertEqual(output.metadata["skill_effort_override"], "high")
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

    def test_session_registry_marks_forked_skill_execution(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_commands_fork"
        if cwd.exists():
            import shutil

            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "context: fork\n"
            "allowed-tools:\n"
            "  - Read\n"
            "  - Bash(git status:*)\n"
            "model: claude-opus-4-6\n"
            "effort: medium\n"
            "---\n\n"
            "Release workflow\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False))
            handled, output = session.handle_repl_command("/ship")

            self.assertTrue(handled)
            self.assertIsInstance(output, CommandExecution)
            assert isinstance(output, CommandExecution)
            self.assertEqual(output.metadata["command_kind"], "skill-fork")
            self.assertEqual(output.metadata["skill_execution_context"], "fork")
            self.assertEqual(output.metadata["skill_model_override"], "claude-opus-4-6")
            self.assertEqual(output.metadata["skill_effort_override"], "medium")
            self.assertEqual(output.allowed_tool_names, ("read_file", "bash"))
            self.assertEqual(output.allowed_bash_command_prefixes, ("git status",))
        finally:
            session.close()
            if cwd.exists():
                import shutil

                shutil.rmtree(cwd)

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
            self.assertEqual(output.metadata["command_policy_name"], "commit")
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
            self.assertEqual(output.metadata["command_policy_name"], "security-review")
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
            self.assertEqual(output.metadata["command_policy_name"], "ultraplan")
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

    def test_review_mode_rejects_non_matching_segment(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Review the diff",
            allowed_tool_names=("bash",),
            allowed_bash_command_prefixes=("git diff", "git show"),
            metadata={
                "command_policy_name": "review",
                "command_policy_source": "repl:/review",
            },
        )

        try:
            def fake_run_query_loop(active_session, prompt: str, sink=None):
                del sink
                self.assertEqual(prompt, "Review the diff")
                allowed = active_session.evaluate_bash_command_policy("git diff | Set-Content out.txt")
                self.assertFalse(allowed.allowed)
                self.assertIn('segment 2 "Set-Content out.txt"', allowed.reason)
                self.assertIn('command mode "review"', allowed.reason)
                return "blocked"

            with patch("claudecode_py.session.run_query_loop", side_effect=fake_run_query_loop):
                result = session.run_command(execution)

            self.assertEqual(result, "blocked")
        finally:
            session.close()

    def test_commit_mode_allows_git_segments_but_rejects_non_git_write_segment(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Create a commit",
            allowed_tool_names=("bash",),
            allowed_bash_command_prefixes=("git add", "git status", "git commit"),
            metadata={
                "command_policy_name": "commit",
                "command_policy_source": "repl:/commit",
            },
        )

        try:
            def fake_run_query_loop(active_session, prompt: str, sink=None):
                del sink
                self.assertEqual(prompt, "Create a commit")
                self.assertTrue(
                    active_session.evaluate_bash_command_policy(
                        "git add demo.txt && git commit -m test"
                    ).allowed
                )
                rejected = active_session.evaluate_bash_command_policy(
                    "git add demo.txt && powershell Set-Content out.txt hi"
                )
                self.assertFalse(rejected.allowed)
                self.assertIn('segment 2 "powershell Set-Content out.txt hi"', rejected.reason)
                return "done"

            with patch("claudecode_py.session.run_query_loop", side_effect=fake_run_query_loop):
                result = session.run_command(execution)

            self.assertEqual(result, "done")
        finally:
            session.close()

    def test_read_only_turn_policy_rejects_complex_write_syntax(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )

        try:
            with session._command_execution_scope(
                allowed_tool_names=("bash",),
                allowed_bash_command_prefixes=("git diff",),
                require_read_only_subagents=True,
                command_policy_name="read-only-turn",
                command_policy_source="advisor-read-only-scope",
            ):
                result = session.evaluate_bash_command_policy(
                    "git diff $(Join-Path $PWD demo.txt)",
                )

            self.assertFalse(result.allowed)
            self.assertIn('command mode "read-only-turn"', result.reason)
            self.assertEqual(result.violating_features, ("command_substitution",))
            self.assertIn("complex_feature=command_substitution", result.reason)
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

    def test_session_run_command_routes_forked_skill_through_child_session(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Run the release workflow",
            allowed_tool_names=("read_file", "bash"),
            allowed_bash_command_prefixes=("git status", "git tag"),
            metadata={
                "command_kind": "skill-fork",
                "command_policy_name": "skill:ship",
                "command_policy_source": "skill:/ship",
                "skill_name": "ship",
                "skill_execution_context": "fork",
            },
        )

        class FakeChildSession:
            def __init__(self) -> None:
                self.contract_kwargs = None
                self.ask_kwargs = None
                self.closed = False

            def set_session_execution_contract(self, **kwargs):
                self.contract_kwargs = kwargs

            def ask(self, prompt: str, sink=None, **kwargs):
                del sink
                self.ask_kwargs = {"prompt": prompt, **kwargs}
                return "forked-result"

            def close(self) -> None:
                self.closed = True

        child = FakeChildSession()

        try:
            with patch.object(session, "create_child_session", return_value=child):
                result = session.run_command(execution)

            self.assertEqual(result, "forked-result")
            self.assertTrue(child.closed)
            assert child.contract_kwargs is not None
            assert child.ask_kwargs is not None
            self.assertEqual(child.contract_kwargs["execution_mode"], "skill-fork")
            policy = child.contract_kwargs["command_policy"]
            self.assertIsNotNone(policy)
            assert policy is not None
            self.assertEqual(policy.name, "skill:ship")
            self.assertEqual(policy.source, "skill:/ship")
            self.assertEqual(policy.allowed_tool_names, frozenset({"read_file", "bash"}))
            self.assertEqual(policy.allowed_bash_command_prefixes, ("git status", "git tag"))
            self.assertEqual(child.ask_kwargs["prompt"], "Run the release workflow")
            self.assertEqual(child.ask_kwargs["allowed_tool_names"], ("read_file", "bash"))
            self.assertEqual(
                child.ask_kwargs["allowed_bash_command_prefixes"],
                ("git status", "git tag"),
            )
            self.assertEqual(child.ask_kwargs["command_policy_name"], "skill:ship")
            self.assertEqual(child.ask_kwargs["command_policy_source"], "skill:/ship")
        finally:
            session.close()

    def test_session_run_forked_skill_mutation_returns_child_delta(self) -> None:
        session = Session(
            SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False),
            persist_transcript=False,
        )
        execution = CommandExecution(
            prompt="Run the release workflow",
            allowed_tool_names=("read_file", "bash"),
            allowed_bash_command_prefixes=("git status", "git tag"),
            metadata={
                "command_kind": "skill-fork",
                "command_policy_name": "skill:ship",
                "command_policy_source": "skill:/ship",
                "skill_name": "ship",
                "skill_execution_context": "fork",
            },
        )

        class FakeChildState:
            def __init__(self) -> None:
                self.messages = [
                    {"role": "user", "content": [{"type": "text", "text": "existing"}]},
                ]

        class FakeChildSession:
            def __init__(self) -> None:
                self.state = FakeChildState()
                self.contract_kwargs = None
                self.ask_kwargs = None
                self.closed = False

            def set_session_execution_contract(self, **kwargs):
                self.contract_kwargs = kwargs

            def ask(self, prompt: str, sink=None, **kwargs):
                del sink
                self.ask_kwargs = {"prompt": prompt, **kwargs}
                self.state.messages.extend(
                    [
                        {"role": "user", "content": [{"type": "text", "text": "child prompt"}]},
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "child answer"}],
                        },
                    ]
                )
                return "forked-result"

            def close(self) -> None:
                self.closed = True

        child = FakeChildSession()

        try:
            with patch.object(session, "create_child_session", return_value=child):
                result = session.run_forked_skill_mutation(
                    execution,
                    tool_name="skill",
                    tool_use_id="tool-1",
                )

            self.assertEqual(result.result_text, "forked-result")
            self.assertEqual(result.injected_message_count, 2)
            self.assertEqual(result.new_messages[0]["source_kind"], "skill_tool_fork")
            self.assertEqual(result.new_messages[0]["source_tool_use_id"], "tool-1")
            self.assertEqual(result.new_messages[0]["skill_name"], "ship")
            self.assertEqual(result.new_messages[1]["content"][0]["text"], "child answer")
            self.assertEqual(result.context_update.skill_name, "ship")
            self.assertEqual(result.context_update.allowed_tool_names, ("read_file", "bash"))
            self.assertEqual(
                result.context_update.allowed_bash_command_prefixes,
                ("git status", "git tag"),
            )
            self.assertTrue(child.closed)
            assert child.ask_kwargs is not None
            self.assertEqual(child.ask_kwargs["prompt"], "Run the release workflow")
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
