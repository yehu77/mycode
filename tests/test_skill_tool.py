from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claudecode_py.config import SessionConfig
from claudecode_py.skills import build_skill_command_execution
from claudecode_py.session import ForkedSkillMutationResult, Session
from claudecode_py.tools.base import ToolContextUpdate, ToolExecutionPayload
from claudecode_py.tools.skill import SkillTool


class SkillToolTests(unittest.TestCase):
    def test_skill_tool_executes_user_invocable_skill_inline(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_tool"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "arguments:\n"
            "  - version\n"
            "model: claude-opus-4-6\n"
            "effort: high\n"
            "---\n\n"
            "Release version: $version\n"
            "Root: ${CLAUDE_SKILL_DIR}\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
            tool = SkillTool()
            ctx = session._runtime_context.tool_context(
                session=session,
                permission_manager=session.permission_manager,
            )
            result = tool.execute({"skill": "ship", "args": "1.2.3"}, ctx)

            self.assertIsInstance(result, ToolExecutionPayload)
            assert isinstance(result, ToolExecutionPayload)
            self.assertEqual(result.result["status"], "inline")
            self.assertEqual(result.result["skill"], "ship")
            self.assertEqual(result.result["injected_message_count"], 1)
            self.assertIn("Release version: 1.2.3", result.new_messages[0]["content"][0]["text"])
            self.assertIn(
                str(cwd / ".claude" / "skills" / "ship"),
                result.new_messages[0]["content"][0]["text"],
            )
            self.assertEqual(result.new_messages[0]["source_kind"], "skill_tool_inline")
            self.assertEqual(result.context_update.skill_name, "ship")
            self.assertEqual(result.context_update.model_override, "claude-opus-4-6")
            self.assertEqual(result.context_update.effort_override, "high")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_skill_tool_returns_structured_mutation_for_forked_skill(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_tool_fork"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "context: fork\n"
            "arguments:\n"
            "  - version\n"
            "---\n\n"
            "Release version: $version\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
            tool = SkillTool()
            ctx = session._runtime_context.tool_context(
                session=session,
                permission_manager=session.permission_manager,
            )

            with patch.object(
                session,
                "run_forked_skill_mutation",
                return_value=ForkedSkillMutationResult(
                    result_text="forked output",
                    new_messages=[
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "child prompt"}],
                            "source_kind": "skill_tool_fork",
                            "source_tool_name": "skill",
                            "source_tool_use_id": "tool-1",
                            "skill_name": "ship",
                            "skill_execution_context": "fork",
                        },
                        {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "child result"}],
                            "source_kind": "skill_tool_fork",
                            "source_tool_name": "skill",
                            "source_tool_use_id": "tool-1",
                            "skill_name": "ship",
                            "skill_execution_context": "fork",
                        },
                    ],
                    context_update=ToolContextUpdate(
                        allowed_tool_names=("read_file",),
                        source="skill_tool_fork",
                        skill_name="ship",
                    ),
                    injected_message_count=2,
                ),
            ) as run_forked:
                result = tool.execute({"skill": "ship", "args": "1.2.3"}, ctx)

            self.assertIsInstance(result, ToolExecutionPayload)
            assert isinstance(result, ToolExecutionPayload)
            self.assertEqual(result.result["status"], "fork")
            self.assertEqual(result.result["skill"], "ship")
            self.assertEqual(result.result["injected_message_count"], 2)
            self.assertEqual(result.new_messages[0]["source_kind"], "skill_tool_fork")
            self.assertEqual(result.new_messages[1]["content"][0]["text"], "child result")
            self.assertEqual(result.context_update.skill_name, "ship")
            run_forked.assert_called_once()
            execution = run_forked.call_args.args[0]
            self.assertEqual(execution.metadata["command_kind"], "skill-fork")
            self.assertEqual(execution.metadata["skill_execution_context"], "fork")
            self.assertIn("Release version: 1.2.3", execution.prompt)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_skill_tool_rejects_unknown_skill(self) -> None:
        session = Session(SessionConfig(cwd=Path(__file__).resolve().parent, interactive=False))
        tool = SkillTool()
        ctx = session._runtime_context.tool_context(
            session=session,
            permission_manager=session.permission_manager,
        )
        try:
            with self.assertRaisesRegex(ValueError, 'Unknown user-invocable skill "missing"'):
                tool.execute({"skill": "missing"}, ctx)
        finally:
            session.close()

    def test_skill_tool_rejects_model_disabled_skill(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_tool_model_disabled"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "disable-model-invocation: true\n"
            "---\n\n"
            "Ship it.\n",
            encoding="utf-8",
        )

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
            tool = SkillTool()
            ctx = session._runtime_context.tool_context(
                session=session,
                permission_manager=session.permission_manager,
            )
            with self.assertRaisesRegex(
                ValueError,
                'Skill "ship" cannot be used with the skill tool due to disable-model-invocation.',
            ):
                tool.execute({"skill": "ship"}, ctx)
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)

    def test_forked_skill_mutation_applies_child_model_and_effort_overrides(self) -> None:
        cwd = Path(__file__).resolve().parent / "_tmp_skill_tool_fork_overrides"
        if cwd.exists():
            shutil.rmtree(cwd)
        (cwd / ".claude" / "skills" / "ship").mkdir(parents=True)
        (cwd / ".claude" / "skills" / "ship" / "SKILL.md").write_text(
            "---\n"
            "description: Ship release\n"
            "context: fork\n"
            "model: claude-opus-4-6\n"
            "effort: medium\n"
            "---\n\n"
            "Ship it.\n",
            encoding="utf-8",
        )

        class FakeChildSession:
            def __init__(self) -> None:
                self.state = type("State", (), {"messages": []})()
                self.ask_kwargs = None
                self.execution_contract = None

            def set_session_execution_contract(self, **kwargs) -> None:
                self.execution_contract = kwargs

            def ask(self, prompt, **kwargs):
                self.ask_kwargs = {"prompt": prompt, **kwargs}
                self.state.messages.extend(
                    [
                        {"role": "user", "content": [{"type": "text", "text": prompt}]},
                        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
                    ]
                )
                return "done"

            def close(self) -> None:
                return None

        try:
            session = Session(SessionConfig(cwd=cwd, interactive=False), persist_transcript=False)
            skill = session.project_context.skills[0]
            execution = build_skill_command_execution(skill, "")
            fake_child = FakeChildSession()

            with patch.object(session, "create_child_session", return_value=fake_child):
                result = session.run_forked_skill_mutation(execution, tool_use_id="tool-1")

            self.assertEqual(fake_child.ask_kwargs["model_override"], "claude-opus-4-6")
            self.assertEqual(fake_child.ask_kwargs["effort_override"], "medium")
            self.assertEqual(result.injected_message_count, 2)
            self.assertEqual(result.new_messages[0]["source_kind"], "skill_tool_fork")
            self.assertEqual(result.new_messages[1]["content"][0]["text"], "done")
        finally:
            session.close()
            if cwd.exists():
                shutil.rmtree(cwd)
